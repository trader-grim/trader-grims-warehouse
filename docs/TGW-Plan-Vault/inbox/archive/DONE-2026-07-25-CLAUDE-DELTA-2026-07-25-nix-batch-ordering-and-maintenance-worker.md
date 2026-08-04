# Nix batch ordering decision — 2026-07-25

The consolidated known-Nix batch is the first executable flake task after the completed tgw-prod Btrfs recovery snapshot and read-only git baseline.

## Why now

It removes the current reproducible-test blockage, establishes the shared evidence/access substrate, and prevents repeated one-off Nix investigations and context/token drain while regular development proceeds.

## Parallelism and gate

Read-only reconciliation, code review, workflow mapping, and other investigation may continue. However, source-fix acceptance, host-dependent feature work, and all additional flake changes remain gated on this batch's evaluation/build evidence, Dave/flake-owner review, controlled switch decision, and post-switch verification.

## Maintenance-worker destination

The flake-maintenance worker is the steady-state replacement for interruption-driven Nix work. Its bounded role is to collect requests, maintain the next-batch inventory, obtain reproducibility/build evidence, prepare review/rollback receipts, and verify stated results after an approved switch. It has no unilateral authority to edit the flake or switch hosts.

New Nix requirements should join the next bounded maintenance batch rather than stalling unrelated development or reopening full planning context each time.
