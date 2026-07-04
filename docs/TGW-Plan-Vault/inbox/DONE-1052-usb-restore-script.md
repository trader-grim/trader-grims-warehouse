# IN PROGRESS — #1052 restore script + README for TGW-VAULT USB

Todo #1052 originally described `TGW-SECRETS-A`/`TGW-SECRETS-B` and
`TGW-SNAPSHOT-0` — those names are stale. Session 38 (2026-06-22)
superseded `TGW-SECRETS` with a single `TGW-VAULT` btrfs USB
(`secrets/`, `dumps/`, `flake/` subvolumes), stamped by
`scripts/tgw-usb-stamp.sh` and auto-triggered by `nix/tgw/usb-vault.nix`.
Writing this packet against current reality, not the stale wording.

**Found while investigating:** `scripts/tgw-restore.sh` (added earlier,
untested) has a real bug — its `--source usb` path does
`cp -rv "$USB_PATH/"* "$BACKUP_DIR/"` (copies `secrets/`, `dumps/`,
`flake/` subdirs as-is) but then looks for the dump at the flat path
`$BACKUP_DIR/tgw-state-machine.pgdump`, which never exists — the real
file is `$BACKUP_DIR/dumps/latest.pgdump` (symlink) or
`dumps/state_machine-<STAMP>.pgdump`. Fixing this + adding the missing
bare-metal (nixos-anywhere) step + a README.

**DONE.** Fixed `scripts/tgw-restore.sh`'s `--source usb` path bug (was
looking for a flat `tgw-state-machine.pgdump` that never existed; now
correctly copies `dumps/`+`secrets/` subvols and follows
`dumps/latest.pgdump`). Added secrets-restore step + permission
re-verification + echo-worker round-trip verification step. Wrote
`docs/TGW-Plan-Vault/reference/TGW-VAULT-RESTORE.md` covering both restore
paths (existing host / bare-metal nixos-anywhere), operator checks
(uid=900, no interactive tgw password, secrets perms), and verification
steps. Live-verified: `bash -n` clean, `--dry-run --source local` runs
end-to-end and fails at the correct, expected step (no local dump present).
Could not live-test `--source usb` (no physical TGW-VAULT stick attached to
this session) — flagging that as the one untested path; worth a real
insert-and-restore drill per PLAN-backup-dr.md's A5 drill policy.
