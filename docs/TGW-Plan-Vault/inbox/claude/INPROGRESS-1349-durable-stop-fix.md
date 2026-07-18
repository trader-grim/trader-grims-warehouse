# In progress: todo #1349 + #1453 (PP-NIXOS-001), nix-flake-maintainer

**Status update:** both fixes built and validated (`nix flake check` + `nixos-rebuild
dry-activate`, both clean/as-expected — see result manifest). NOT committed, pushed, or
switched — awaiting Dave's approval to commit, and separately his confirmation of a safe
time to `nixos-rebuild switch` on tgw-prod (stops 3 live worker services). Full detail:
`docs/TGW-Plan-Vault/plan/packets/results/1349-RESULT.md`.

## #1349 — durable-stop fix for catalog_rebuild/ebay_legacy_sync/ebay_sync

Root cause confirmed live on tgw-prod: the #1322 durable-stop mechanism
(`services.tgw.workers` declarative inclusion list in
`~/tgw-flake/nix/hosts/tgw-prod.nix`) already exists and works for the 5
workers excluded on 2026-07-12 13:58 (`pm_intake`, `thumbnail_gen`,
`velocity_stats`, `ebay_price_reducer`, `ebay_sku_migrate`). But
`catalog_rebuild`/`ebay_legacy_sync`/`ebay_sync` were deliberately left IN
the enabled list by that same commit (comment: "crash/fail on their own...
not administratively excluded") — 7 hours later the
2026-07-12 20:51 catalog-rebuild-loop incident happened (eBay 25707 orphaned
SKU → per-SKU fallback sweep → nonstop catalog_rebuild cascade), and those
three were stopped live (`systemctl stop`, not disable — never made
durable, per the incident writeup itself). Confirmed live today
(`systemctl is-enabled` = enabled, `ActiveEnterTimestamp` = 2026-07-14,
i.e. resurrected by a rebuild after the live stop, exactly the recurring
bug class).

Fix: add these three to the same declarative exclusion in
`nix/hosts/tgw-prod.nix`. This is a live-switch-affecting change (currently
running services) — dry-activate/build-check only per profile constraints,
NOT switching without Dave's go.

## #1453 — misleading sway.nix:69 comment

Per `INCIDENT-2026-07-16-kdeconnect-clipboard-triage-failure.md`: lan-mouse
has no clipboard-sync feature (confirmed via code archaeology that session).
`nix/os/sway.nix:69`'s comment wrongly attributes `wlr-data-control` xdg-
portal support to "lan-mouse clipboard sync." Correcting the comment.

Branch-per-task note: this profile (nix-flake-maintainer) does not use the
tgw-coder worktree convention — mutation of `~/tgw-flake` is gated by the
profile's own drift-check (Step 1) + commit procedure (Step 2), not a
worktree. Work happens directly on `~/tgw-flake` master, per profile
definition. Result manifest written to
`docs/TGW-Plan-Vault/plan/packets/results/1349-RESULT.md` on this repo's
current branch (`catio-nix-0.0.1-alpha`).
