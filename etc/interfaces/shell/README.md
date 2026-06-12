# TGW Shell Interface — `tgw.source` / `tgw-dev.source`

Version-controlled copies of the operator shell layer that lives at
`/opt/TGW/bin/` and is sourced from the `tgw` user's shell profile
(PP-SHELL-001 Tier 3).

| File | Lines | Role |
|------|-------|------|
| `tgw.source` | 2898 | Stable interactive helpers — environment setup, camera / KDE Connect controls, whisper dictation, file-manager / browser launchers, current-item management, catalog search (direct-JSON readers), backup/sync, location labels, utilities. |
| `tgw-dev.source` | 140 | Development/bootstrap layer — project venv activation, environment info, Python runner. |

These are **sourced, not executed** (`source /opt/TGW/bin/tgw.source`). Load
order: `tgw-dev.source` (bootstrap) then `tgw.source` (helpers).

## Architecture compliance (the `tgw`-api fence)

Settled rule: *all ItemData reads/writes go through the `tgw` CLI / tgw-api* —
no bash function should mutate `ItemData/<SKU>/<SKU>.json` directly.

**All ARCH-VIOLATES write functions are wrapped** (Tier 2, verified Tier 3) —
each is now a thin one-liner over the `tgw` CLI:

| Function (live line) | Wraps |
|----------------------|-------|
| `hintupdate` (201) | `tgw hint` |
| `locationupdate` (1046) | `tgw locationupdate` |
| `statusupdate` (1049) | `tgw statusupdate` |
| `titleupdate` (1052) | `tgw titleupdate` |
| `verifiedupdate` (1056) | `tgw verifiedupdate` (loops over args, writes `verified`) |
| `catlocmvall` (1066) | `tgw catlocmvall` |

The remaining `jq … > tmp && mv` direct-JSON writes in the file
(`fixsku`, the qty-decrement on sale, the legacy `description` / `"C:Location"`
draft writers) belong to **DEPRECATED** pipelines documented in
`docs/TGW-Plan-Vault/reference/SHELL-AUDIT.md` (old CSV-merge / old draft /
one-time fixups). They are slated for *removal* in the Tier-2 cleanup pass, not
for wrapping — they are not part of the active intake/edit path. See SHELL-AUDIT
for the full KEEP / WRAP / DEPRECATED inventory.

## Deployment (operator-gated)

The live `/opt/TGW/bin/` files are owned by `tgw` and drive Dave's daily shell;
**cutover is operator-controlled.** These repo copies are the reviewable source
of truth. To deploy (backs up existing, replaces only when changed):

```bash
sudo bash /opt/TGW/src/trader-grims-warehouse/etc/interfaces/install.sh
# then, in open shells:
source /opt/TGW/bin/tgw.source
```

The installer writes `tgw.source.bak-<timestamp>` before any replacement. As of
this checkpoint the repo copies are byte-identical to the live files (sha256
verified), so the first install is a no-op — it establishes the deploy path for
future reviewed edits.
