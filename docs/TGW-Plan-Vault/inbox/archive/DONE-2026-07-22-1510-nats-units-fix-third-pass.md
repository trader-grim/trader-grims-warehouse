# Todo #1510 (PP-AIOPS-001) — third pass on NATS storage-unit bug, DONE pending Dave review

**Status:** Fix committed locally on tgw-prod (`~/tgw-flake` @ `c4e942d`). NOT pushed,
NOT switched — Dave explicitly held tonight's second rebuild cycle for his own review.

## What was actually wrong (both prior attempts were wrong theories)

- Attempt 1 (already-switched commit `bc2b67c`... actually the 50G->10G magnitude
  fix): fixed the *magnitude* (50GB > 37GB free disk) but not the real bug.
- Attempt 2 ("use the same suffix text `G` on both sides" — the comment already in
  `nix/nats.nix` before this session): still failed live. `nats.service`'s JSON
  config parser and `natscli`'s Go flag parser interpret the SAME literal text "10G"
  under different conventions — decimal (1e9) server-side, binary (2^30) client-side.
  Confirmed live: `nats.service` startup log said "Max Storage: 10.00 GB" (decimal);
  `nats-stream-init` failed with `MaxBytes: 10737418240` (binary) against a
  10,000,000,000-byte account ceiling — error 10047, insufficient storage.
- Real fix (this session): raw byte integers, no unit suffix, on both sides.
  `10000000000` everywhere. Confirmed via nixpkgs module source
  (`services.nats.settings` is `pkgs.formats.json {}`, so a Nix int becomes a
  literal JSON number, no string/suffix parsing at all) and via natscli's own
  `--max-bytes=BYTES` flag semantics (bare int = raw byte count).

## Verification performed (not just dry-activate this time)

1. `nix flake check` — clean.
2. `sudo nixos-rebuild dry-activate --flake "path:/home/db/tgw-flake#tgw-prod"` —
   clean; new store path `/nix/store/cj10ilxv0dxgp0v71mgc9cvrfnl8fqn5-nixos-system-tgw-prod-25.05.20260102.ac62194`.
   `validate-nats-conf.drv` (i.e. `nats-server -t`) passed, confirming the new JSON
   config is valid to the real binary.
3. Inspected the built `nats.conf` directly: `"max_file": 10000000000` as a JSON
   number, no string/suffix.
4. **Live-tested against the already-running tgw-prod broker** (unaffected by this
   config change, which needs a switch) using a throwaway stream
   `TEST_UNITBYTES_1510` with `--max-bytes 10000000000` (same value the fixed
   script will use) — `nats stream info --json` reported `config.max_bytes:
   10000000000`, exact match to the intended ceiling. Stream deleted afterward
   (`nats stream rm TEST_UNITBYTES_1510 --force`) — confirmed via `nats stream ls`
   that only `ITEMDATA_MUTATIONS` and `QUEUE_TRANSITIONS` remain.

## Drift note (unrelated, flagged not blocking)

a1131's `~/tgw-flake` checkout has one local commit (`5c3a9e9`, "feat(a1131): add
hermaroid test account, db to its group") not yet on `origin/master` and not queued
via `tgw flake request-push`. Unrelated to this fix (different file, doesn't touch
`nix/nats.nix`), but flagging per invariant E10 rather than silently proceeding.
Not reconciled this session — my scope was tgw-prod's `nix/nats.nix` only.

## Next step

Per Dave: do NOT `request-push`/`request-switch` tonight — commit only, awaiting his
review. When he gives the go-ahead: `tgw flake request-push` then
`tgw flake request-switch` (both as `nix-flake-maintainer`/gated procedure), report
job ids, stop — a human runs the actual push/switch and calls `mark-executed`.
