# Result: 1349 durable-stop fix (catalog_rebuild/ebay_sync/ebay_legacy_sync) + 1453 sway.nix comment fix
Status: **built, validated (dry-activate), NOT yet committed/pushed/switched — awaiting Dave's approval**
Todo: #1349, #1453   PP: PP-NIXOS-001

## Branch-per-task note
This packet was dispatched with tgw-coder's worktree-per-task instruction
(`todo/1349-durable-stop-fix` off `catio-nix-0.0.1-alpha`), but the actual work is on
`~/tgw-flake` (a separate repo from `trader-grims-warehouse`), which this profile
(nix-flake-maintainer) explicitly does **not** manage via worktree-per-task — its
mutation surface is `~/tgw-flake` directly on tgw-prod, gated by the profile's own
Step 1 (drift check) + Step 2 (commit procedure), not a worktree (see the profile's own
header doc). Work was done directly on `~/tgw-flake`'s `master` branch, uncommitted, per
that contract. This result manifest lives on `trader-grims-warehouse`'s current branch
(`catio-nix-0.0.1-alpha`) as directed.

## Step 1 — drift check (both hosts, before any edit)
```
tgw-prod:  git fetch origin && git log --oneline origin/master..HEAD  → (empty, clean)
a1131:     git fetch origin && git log --oneline origin/master..HEAD  → (empty, clean)
```
No drift on either host. Both match `origin/master` at commit `46f2c1d`.

## #1349 — root cause (confirmed live, not hypothetical)

The `services.tgw.workers` declarative-inclusion mechanism from #1322 (built
2026-07-12 13:58:40, commit `1a3285c`, `nix/hosts/tgw-prod.nix`) already exists and
already works — for the 5 workers it excludes (`pm_intake`, `thumbnail_gen`,
`velocity_stats`, `ebay_price_reducer`, `ebay_sku_migrate`). It is **not** a
documentation-only fix; it's read by `nix/tgw.nix`'s `enabledWorkers` filter and
mechanically determines which `tgw-worker@<queue>.service` units get generated at all.

The gap: that same commit *deliberately left* `catalog_rebuild`/`ebay_legacy_sync`/
`ebay_sync` in the enabled list, reasoning (its own comment) that they "crash/fail on
their own, a separate known-red condition... not administratively excluded." ~7 hours
later that same day, the 2026-07-12 20:51 incident
(`docs/TGW-Plan-Vault/inbox/archive/INPROGRESS-catalog-rebuild-loop-incident.md`)
showed why that call was wrong: `ebay_sync`'s bulk `fetch_all_offers` has been failing
on an orphaned bad-SKU offer (eBay error 25707, todo #1077, **still unfixed**) since
before 2026-07-12. The bulk-fetch failure triggers a per-SKU fallback sync throttled to
once/24h; each time that throttle expires it re-sweeps the full 55K+ item catalog,
firing a `catalog_rebuild` job (~57s) every 30-90s, back to back, indefinitely.

Those three were stopped live that evening (`systemctl stop`, explicitly **not**
`disable` — the incident writeup itself flags "durable-stop fix is separate work under
#1322"). Because `services.tgw.workers` was never updated to match, the next
`nixos-rebuild switch` (confirmed via `systemctl show -p ActiveEnterTimestamp` = 2026-07-14
on all three units) silently resurrected all three — running unnoticed for 4 days until
todo #1349 caught it again. This is literally the "different worker set each time"
recurrence CLAUDE.md already flags: not a one-off, this is the second full instance of
the same bug class after #1322 shipped, from the same commit that shipped the fix.

**Live confirmation before the fix (today):**
```
$ systemctl is-enabled tgw-worker@catalog_rebuild.service tgw-worker@ebay_legacy_sync.service tgw-worker@ebay_sync.service
enabled
enabled
enabled
$ systemctl show tgw-worker@ebay_sync.service -p ActiveEnterTimestamp
ActiveEnterTimestamp=Tue 2026-07-14 17:24:19 PDT
```
All three running, declaratively enabled — confirms the mechanism was incomplete, not
missing.

## #1349 — fix

`~/tgw-flake/nix/hosts/tgw-prod.nix`: moved `catalog_rebuild`, `ebay_legacy_sync`,
`ebay_sync` out of `services.tgw.workers` (the enabled-worker inclusion list) into the
same durable-exclusion comment block as the original 5, with the full incident
chain/rationale documented inline and an explicit "do not re-add until todo #1077 is
fixed" note so this isn't silently reversed by a future rebuild-and-forget cycle.

No new Nix mechanism was needed — the #1322 mechanism is sound and mechanically
enforced (invariant-E11-shaped: declarative config, not a written rule an agent has to
remember to follow). The bug was in applying it, not in the mechanism itself.

## #1349 — acceptance evidence (dry-activate, no live switch)

```
$ nix flake check
... (all outputs including nixosConfigurations.tgw-prod evaluate clean, exit 0)

$ sudo nixos-rebuild dry-activate --flake "path:$HOME/tgw-flake#tgw-prod"
building the system configuration...
would stop the following units: tgw-worker@catalog_rebuild.service, tgw-worker@ebay_legacy_sync.service, tgw-worker@ebay_sync.service
would activate the configuration...
Done. The new configuration is /nix/store/0cbj09pjf21gwvs9r0ra10k2g84rs1mj-nixos-system-tgw-prod-25.05.20260102.ac62194
```

Exactly the intended effect and nothing else — `dry-activate` shows only those three
units being stopped, no other unit churn. `a1131`'s host module
(`nix/hosts/a1131.nix`) does not set `services.tgw.workers` at all (no worker fleet on
that host), so this change is tgw-prod-only and a1131 needs no equivalent edit.

**Not yet done, on purpose:** `sudo nixos-rebuild switch` was NOT run. This change stops
three currently-running live services on tgw-prod (a host running a live graphical
session per this profile's own safety rule) — per the packet's explicit instruction,
this needs Dave's go before switching, not just before merging. Also: no commit has been
made — Dave has not yet approved committing/pushing this shared-infra change (constraint:
"only commit when Dave has approved it").

## #1453 — fix

`~/tgw-flake/nix/os/sway.nix:69` (line drifted from #1453's originally-cited line but
same location — it's the comment directly above `xdg.portal = { ... }`). Old comment:

```
# xdg-desktop-portal: needed for screen share, file pickers, and
# wlr-data-control (used by lan-mouse clipboard sync).
```

Per `docs/TGW-Plan-Vault/inbox/archive/INCIDENT-2026-07-16-kdeconnect-clipboard-triage-failure.md`:
"the lan-mouse 'clipboard sync' finding (never implemented, ever) was correct... the
`nix/os/sway.nix:69` comment attributing clipboard sync to lan-mouse's `wlr-data-control`
xdg-portal note is misleading and should eventually be corrected." lan-mouse has no
clipboard-sync feature at all (confirmed via code archaeology in that incident) — the
comment falsely implied it does. New comment corrects this and cites the incident so the
history isn't lost:

```
# xdg-desktop-portal: needed for screen share (grim/slurp), file pickers,
# and general wlr portal backend support (wlr.enable → wlr-data-control).
# NOTE (corrected 2026-07-18, todo #1453): this does NOT provide
# cross-machine clipboard sync. lan-mouse has no clipboard-sync feature —
# confirmed via code archaeology during the 2026-07-16 kdeconnect-clipboard
# triage incident (see INCIDENT-2026-07-16-kdeconnect-clipboard-triage-
# failure.md). The previous comment here wrongly attributed wlr-data-control
# to "lan-mouse clipboard sync"; nothing in this stack currently consumes it
# for clipboard purposes.
```

`nix flake check` (run above, covers all files including `sway.nix`) passed clean —
purely a comment change, no functional diff.

## Files touched (both on `~/tgw-flake`, uncommitted)
- `nix/hosts/tgw-prod.nix` — durable-exclusion list extended (#1349)
- `nix/os/sway.nix` — comment corrected (#1453)

## Deviations from spec
1. **Packet's worktree/branch-per-task instruction not followed** — this profile
   (nix-flake-maintainer) doesn't use that convention for `~/tgw-flake`; used its own
   drift-check + commit-procedure gate instead. Flagged per this profile's own
   instruction to note the discrepancy rather than silently apply a mismatched
   convention. `trader-grims-warehouse` repo itself is already on `catio-nix-0.0.1-alpha`
   (not `main`), so no new branch was created there either — this manifest was added to
   the current branch directly.
2. **No live `nixos-rebuild switch`, per the packet's own explicit instruction** — see
   "Not yet done, on purpose" above. This is not a deviation from spec, it's compliance
   with the packet's own stated constraint, called out here so it isn't mistaken for an
   incomplete task.
3. **No commit made** — same reason, "only commit when Dave has approved" + the packet's
   own note that live-switch-affecting changes need Dave's go.

## What Dave needs to decide
1. **Approve committing** `nix/hosts/tgw-prod.nix` + `nix/os/sway.nix` on `~/tgw-flake`
   (message would be `fix(#1349,#1453): durable-stop catalog_rebuild/ebay_sync/ebay_legacy_sync; correct misleading sway.nix clipboard comment`).
2. **Confirm a safe time** to run `sudo nixos-rebuild switch --flake path:~/tgw-flake#tgw-prod`
   on tgw-prod — this is a live graphical-session host per this profile's standing rule,
   and the switch will stop three currently-running worker services.
3. Separately (not in this packet's scope): todo #1077 (orphaned bad-SKU offer, eBay
   25707) is the actual root fix — until it's cleared, re-enabling these three workers
   will repeat the same cascade on the next 24h throttle expiry.

## Out-of-scope findings filed
None new — the incident's own "Open follow-ups" already named todo #1077 (root cause,
still open) and a full audit of all supposedly-stopped workers' actual systemd
enablement state (this packet's #1349 work partially serves that audit for the 8 workers
now covered by `services.tgw.workers`'s exclusion; no further currently-stopped-but-
undeclared workers were found on tgw-prod during this check — `alt_text` was the only
other list change and it's a legitimate 2026-07-14 addition, not a drift case).
