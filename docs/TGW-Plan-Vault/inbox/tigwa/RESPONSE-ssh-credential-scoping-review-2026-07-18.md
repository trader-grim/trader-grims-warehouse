# RESPONSE — review of TIGWA-SSH-CREDENTIAL-SCOPING-PROPOSAL-2026-07-18

**From:** Claude (flake-owner review pass)
**To:** Tigwa / Dave
**Re:** `dev-workflow/research/TIGWA-SSH-CREDENTIAL-SCOPING-PROPOSAL-2026-07-18.md`, #1459, PP-HR-001
**Status:** review only — no account/key/sudoers/flake/service change made

## Verdict: architecture is sound, recommend proceeding to Stage 0

1. **New identity vs. audited reuse of `tigwa`:** agree with the proposal's own instinct — new dedicated identity (`tigwa-observe` or similar), not the existing `tigwa` nologin account. Reusing it would conflate a locally-scoped service account (already provisioned, home-dir owned, lingering enabled per today's account-foundation check) with an SSH-exposed automation boundary. Keep them separate; the existing `tigwa` account's role should stay "local Hermes service identity," not "remote dispatch target."
2. **First-cut registry is appropriately narrow.** The three included reads (plan-vault-metadata, thermal-evidence, nvme-temperature) match what's actually being consumed today per the verified caller table. Deferring `todo-tigwa-summary-v1` until the CLI/DB execution path is separately analyzed is correct — that's exactly the kind of operation that looks read-only but risks tunneling broader `tgw` argv if rushed.
3. **`command=` dispatcher design is the right mechanism** and matches the already-adopted E11 pattern (mechanical enforcement over prose trust) — this is the same lesson as the PreToolUse flake-guard hook, applied to a new surface.
4. **Staged migration (shadow-read → narrow callers → revoke old path) is correct ordering** — it doesn't require choosing between safety and availability at any single step, and the rollback path preserves a human break-glass rather than silent re-grant.

## Two things worth tightening before Stage 0 build starts

- **NVMe temperature read** is the one operation still requiring `sudo` (`smartctl`). Recommend investigating a udev rule granting the new identity direct unprivileged read access to the specific NVMe temperature sysfs/health attribute *before* falling back to a scoped sudo rule — a working non-sudo path is strictly better for the "cannot obtain sudo, full stop" invariant.
- **Dispatcher hostile-input test list** (empty, unknown, argument-bearing, shell metacharacters, stdin, env injection) is good; add one more: verify the dispatcher's own `command=` binding survives an `authorized_keys` file edit that accidentally drops the restriction (i.e. a deploy-time regression test on the key-options string itself, not just the dispatcher binary).

## Answering the five review questions

1. Separate identity + dispatcher is the right boundary, not audited reuse — see above.
2. First-cut registry and privilege classifications are complete and appropriately narrow, with the sudo caveat on NVMe noted above.
3. Staged shadow-read/fail-closed/break-glass migration is acceptable as designed.
4. `E11-TIGWA-REMOTE-CAPABILITY`'s six deterministic checks make the contract mechanical, not prose — matches the same standard already applied to the flake-guard hook.
5. Flake placement: declare the dispatcher + authorized_keys restriction in the Nix flake (not hand-edited on the host), with a build-time or periodic test asserting the deployed `authorized_keys` options string matches the flake-declared value byte-for-byte — that's the drift-prevention mechanism the proposal doesn't fully specify yet.

## Decision still needed from Dave

- Approve new-identity-vs-reuse (I recommend new identity, above).
- Confirm first cut stays entirely read-only.
- Name the human break-glass owner.
- Decide whether any tracker-write capability is ever wanted, or stays permanently out of scope.

No build begins on my end until Dave confirms these.
