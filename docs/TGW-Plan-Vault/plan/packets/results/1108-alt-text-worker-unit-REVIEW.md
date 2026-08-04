status: cleared
reviewer: Claude (main session, catio-nix-0.0.1-alpha)
todo: #1108   pp_ref: PP-DATALEARN-001
branch: todo/1108-alt-text-worker-unit @ 53baa73 (this repo — doc-only;
        real change is in the separate ~/tgw-flake repo, branch
        todo/1108-alt-text-worker-unit @ b60508a)

## Manifest says "partial" — reality has since moved past it
The manifest (committed 2026-07-14 17:56:30 by Dave) states `nixos-rebuild
switch` was deliberately NOT run this session, leaving Acceptance items
3-5 (live unit, queue drain) open for Dave to apply manually. Independent
live check during this review shows the switch **was** subsequently run:

- `~/tgw-flake` repo: `master` already contains commit `b60508a` (the
  packet's own commit — fast-forwarded onto master directly, no pending
  merge needed).
- `nixos-rebuild list-generations`: generation 85, built
  2026-07-14 17:58:15 — 2 minutes after the flake commit.
- `systemctl status tgw-worker@alt_text.service`: `active (running)`,
  up 1h19m at time of check.
- `queue_jobs` for `alt_text`: 5 `succeeded`, 4 `dead_letter` (0 `queued`
  left) — the originally-stuck 5 jobs drained. The 4 dead-letters are the
  same `MasterArchive`-unmounted `WORKER_EXCEPTION` crashes documented and
  already fixed by the separately-reviewed #1407 packet (same 4 SKUs,
  `tgw202606021107459`/`tgw202605051933258`/`tgw202605060201087`/
  `tgw202605052242107`), not a new problem introduced by this packet.

This matches the "probably an interruption" read: Dave ran the switch
himself shortly after writing the manifest and before updating it to
reflect the result — the manifest is stale, not wrong about what it
documents.

## Checked
- Diff scope: this repo's branch adds only the result manifest doc — no
  source changes, matching the packet's own framing (spec content lives
  entirely in the flake repo). Flake-repo diff (from the manifest, cross-
  checked against the live generation 85 config): `workerScripts.alt_text`
  added to `nix/tgw.nix`, `"alt_text"` added to `services.tgw.workers` in
  `nix/hosts/tgw-prod.nix` with an explanatory comment — exactly the
  packet's spec items 1-2, nothing else. `a1131.nix` correctly left
  untouched (checked, no worker config there).
- `nix flake check` clean per manifest (exit 0, all three
  `nixosConfigurations` including `tgw-prod` evaluated without error).
- No out-of-scope files touched.
- Full offline suite: manifest reports `2245 passed, 1 skipped` on a
  baseline-clean worktree (no source diff to drive regressions) — accepted
  as-is, not independently re-run (no source change in this repo to
  regress).
- No invariants.md entries implicated (systemd/flake wiring only, no
  data-path code).
- No premature production write — the switch that was eventually run is
  literally this packet's own deliverable, not an out-of-scope action.

## Summary
Fully complete, ahead of what its own manifest claims. Cleared for stitch.
Flake-repo side needs no further action (already on master, already live).
This repo's side just needs the manifest-doc branch merged for
recordkeeping.
