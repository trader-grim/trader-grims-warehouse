# Addendum — a1131 production state-machine client in the first Nix batch

## Verified current state

- a1131's flake host is deliberately `bases/portable.nix`: it enables the TGW module but sets `workers = []`, `enableHttp = false`, and `enablePostgres = false`.
- The full `nix/tgw.nix` state-machine/PostgreSQL/worker module exists, but its package option is explicitly not yet wired; the current runtime uses an out-of-band venv path.
- On a1131 now, `tgw` is not installed and the Nix profile contains no TGW package.

## First-batch addition

Install the flake-built `tgw`/state-machine **client** package declaratively on a1131, and prove it can read the canonical tgw-prod `state_machine` ledger through the approved configuration/auth path.

This means “production state machine on the laptop” as the same production code and protocol pointed at the one authoritative production ledger—not a second local PostgreSQL database, worker fleet, or competing queue authority. The latter would require separate replication, conflict, recovery, and authority decisions and is intentionally out of scope for this first batch.

## Required verification

- a1131 receives the package through the reviewed flake switch, not `pip` or a one-off Nix profile install;
- `tgw` is available to the intended user;
- a bounded read-only state-machine query against tgw-prod succeeds;
- no a1131 PostgreSQL service, TGW worker, HTTP service, or external-action capability starts as a side effect;
- configuration/auth material remains least-privilege and non-secret output paths remain separated.
