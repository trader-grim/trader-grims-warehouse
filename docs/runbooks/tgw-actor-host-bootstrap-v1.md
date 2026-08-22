# TGW actor-host bootstrap and recovery — v1

## Boundary

`tgw-install-actor-host` installs only the stable systemd unit and tmpfiles
declaration from the immutable release selected at
`/opt/TGW/tgw-lib/actor-runtime/current`. Run it as root on the Debian
`tgw-lib` host. It does not select a release, create the W17 admission signing
key, generate actor contracts, or start the provider.

The operation writes its hash-bound receipt before changing host files. Keep
`/opt/TGW/tgw-lib/var/host-bootstrap-receipts` intact: a receipt with status
`PREPARED` is recoverable partial work, `INSTALLED` is the completed host
installation, and `ROLLED_BACK` is terminal evidence.

## Install

1. Verify that `actor-runtime/current` selects the exact reviewed immutable
   release and that the provider configuration has been prepared separately.
2. Choose a new stable operation identifier. Never reuse an identifier from a
   prior install or rollback.
3. Run:

   ```text
   sudo tgw-install-actor-host install --operation-id <operation-id>
   ```

4. Inspect `<operation-id>.json` in the receipt root. Continue only when its
   status is `INSTALLED` and `systemctl is-enabled
   tgw-actor-fleet-provider.service` reports `enabled`.
5. Start the service only as the separately bound fleet-refresh procedure
   directs; installation alone intentionally does not start it.

Repeating the same completed install is idempotent only while the exact
release, installed files, receipt hash, and service enablement remain intact.
Any mismatch holds as an operation-id collision.

## Recover or roll back

Use the original receipt whether its status is `PREPARED` or `INSTALLED`:

```text
sudo tgw-install-actor-host rollback \
  --receipt /opt/TGW/tgw-lib/var/host-bootstrap-receipts/<operation-id>.json
```

Rollback refuses changed artifacts. On success it restores the prior files,
bounded tmpfiles-created directories, systemd enablement, and service activity;
writes `<operation-id>.rollback.json`; and changes the original receipt to
terminal `ROLLED_BACK`. Repeating that rollback returns the existing verified
rollback receipt. A later install requires a fresh operation identifier.

If rollback reports `HOLD`, do not delete or edit either receipt and do not
retry under a new identifier. Preserve the exact files and systemd state for
operator reconciliation.
