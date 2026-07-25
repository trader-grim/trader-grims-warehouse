# Nix flake batch request — known required changes, 2026-07-25

**Decision:** Combine all currently known necessary flake-owned changes into one task, one flake-owned branch, one review/build evidence set, and one deliberate host-switch/rollback window. Do not create incidental one-off flake edits.

## Bounded initial inventory

1. Add the dev-shell dependencies needed for the project test path: `python-multipart` and `mistune`.
2. Repair or explicitly remove the source-checkout flake evaluation's unresolved `home` path reference. Current evidence: `nix flake show` from `/opt/TGW/src/trader-grims-warehouse` fails with `Path 'home' does not exist in Git repository`.
3. Implement the approved persistent Dave↔Tigwa group/shared-access and non-secret shared-output-root design from `CLAUDE-REQUEST-2026-07-25-tigwa-group-and-shared-library-access.md`.
4. Include Portable Catalog host/package/module changes only after the required inventory identifies their exact, reproducible form.

## Required acceptance evidence

- `nix flake check` and targeted evaluations/builds for every affected host/module.
- `nix develop … -c pytest` runs without adding ephemeral packages; current focused evidence is that `python-multipart` and `mistune` are absent from the declared dev environment.
- The source-checkout flake behavior is explicit and valid.
- Both intended actors can use the approved shared-access/output path without widening secret access.
- A single host-switch plan, explicit rollback, and post-switch capability checks.

## Boundary

Do **not** add unevidenced future Nix work to this batch. Backup timer deployment, worker changes, and new Nix requests remain separate proposed items until they are factually established and Dave adds them to a future batch.
