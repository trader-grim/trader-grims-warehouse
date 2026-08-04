# Portable fleet buildout — program launch, 2026-07-25

**Decision:** Begin the full portable buildout. The portable fleet is a product/operations program, not a collection of independently installed apps.

## Authority and architecture

- **One production authority:** tgw-prod retains the canonical PostgreSQL `state_machine`, catalog, workers, external-action authority, and evidence library.
- **Portable clients:** laptops, tablets, and capture devices receive scoped packages/apps and communicate through approved APIs/configuration. They do not start competing production PostgreSQL, worker, or eBay authority.
- **Private-network substrate:** every fleet device receives a named Tailscale identity, defined role, least-privilege policy, owner/custodian, lifecycle/revocation record, and verification receipt.
- **Separate apps, shared contracts:**
  1. `tgw` state-machine/operations client — first Nix-installed portable capability on a1131 and later laptops;
  2. Flutter Portable Catalog — browse/edit/offline-cache and controlled outbox workflow (PP-PORTABLE-CATALOG-001);
  3. native Kotlin Camera/Intake app — barcode/photo/video/location/attribute capture (PP-INTAKE-004), standalone first and event-bus participation later.
  Do not collapse the Flutter catalog app and native camera app into one scope.

## Fleet cohorts and delivery order

1. **Foundation / Nix batch:** resolve source/flake reproducibility, dependencies, shared non-secret output path, and install the Nix-built `tgw` client on a1131 against canonical tgw-prod state.
2. **Laptop cohort:** Dave laptop and shipping laptop. Enroll Tailscale, record device identity/role, install the appropriate client package, prove authenticated read-only state-machine access, and define shipping-only permissions/workflow separately from general administration.
3. **Catalog tablet pilot:** enroll one named tablet; install/test the Flutter catalog app against the real but scoped service; prove online browse, offline cached browse with a visible freshness marker, and a controlled outbox/reconnect scenario before broad tablet rollout.
4. **Capture cohort:** camera/other tablets. Enroll one designated capture device; implement/verify the native intake app’s standalone capture path before event-bus/remote-control additions. Preserve raw capture provenance and explicit operator acceptance for state-changing intake.
5. **Expansion:** add remaining tablets/cameras only from a verified cohort template, with each device receiving its own enrollment and acceptance receipt.

## Per-device enrollment record and gate

Before a device is called operational, record: human-friendly name, stable Tailscale node/device identity, owner/custodian, physical role/location, OS/version, app/package versions, permitted services/actions, data classification/cache policy, offline behavior, revocation/loss procedure, and last verification time.

Required acceptance for each device:
- appears in the approved tailnet under its intended name and least-privilege policy;
- reaches only its approved TGW service surface over Tailscale;
- has the correct client/app installed from the approved package/release path;
- demonstrates its role with a bounded fixture or read-only production check;
- demonstrates offline/degraded behavior where applicable;
- has documented revoke/wipe/replace recovery.

## Immediate blockers / next evidence

- a1131 and tgw-prod are currently healthy Tailscale nodes in the same tailnet; no laptop, shipping laptop, tablet, or camera node is presently visible from this inventory.
- Device enrollment requires physical-device access and an authenticated Tailscale enrollment action; do not place auth keys or account secrets in chat or Plan Vault.
- Flutter app, native intake app, and shipping workflow need a named first pilot device and its actual OS/hardware facts before installation commands or permissions are selected.

## Non-goals for the first cohort

No second production state machine, no ambient camera/clipboard collection, no bulk device enrollment without inventory, no eBay/listing authority on a new device, and no background worker/agent activation merely because a device joins Tailscale.
