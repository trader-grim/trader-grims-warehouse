# Request: persistent Dave ↔ Tigwa access and library convergence

**Requested by:** Dave Buko
**Date:** 2026-07-25
**Type:** Nix-owned access/design packet — no direct local permission workaround

## Decision and intent

Dave needs reliable access to Tigwa-produced, non-secret working material on both a1131 and tgw-prod. He has repeatedly requested this and cannot make a durable change himself at present because the required user/group/home/access configuration is Nix-owned.

This is not a request to expose Tigwa's whole home or Hermes secrets. It is a request to stop requiring Dave to remember which host created a useful file, and to converge accepted material on the shared library.

## Read-only observations captured now

### a1131

- `db` exists (uid 1000); `tigwa` exists (uid 1001).
- There is no locally resolved `tigwa` group.
- `db` is not currently a member of a `tigwa` group.
- `/home/tigwa` and `/home/tigwa/.hermes` are mode `700` and owned by `tigwa:users`.
- The current Tigwa execution plan is `/home/tigwa/.hermes/plans/2026-07-25_150428Z-tgw-claude-wake-execution-plan.md`, mode `600`; Dave cannot traverse/read it through the current home boundary.

### tgw-prod

- `db` is already a member of the `tigwa` group.
- `/home/tigwa` is `tigwa:tigwa`, mode `750`, so group traversal is available, but individual file mode still governs readable content.
- The shared Plan Vault is group-managed (`tgw:tgw`, mode/ACL `2770`) and is the appropriate canonical/library substrate, not a substitute for explicitly shared Tigwa workspace output.

## Requested bounded design/build packet

1. Define a persistent Nix-managed access policy for `db` ↔ Tigwa on **both** hosts. On a1131 this likely needs a real `tigwa` group and `db` membership; verify the existing tgw-prod membership/configuration rather than assuming it is durable.
2. Define a dedicated, group-readable **non-secret shared-output root** for Tigwa on each host, with a stable logical role and safe ownership/mode/default ACL or group-inheritance behavior. Do not make `/home/tigwa`, `.hermes` configuration, credential material, session databases, or arbitrary caches broadly readable.
3. Define which outputs are eligible for that root: reviewable plans, reports, packets, generated non-secret evidence, and read-only status artifacts. Explicitly exclude credentials, tokens, private browser/session material, raw secrets, and sensitive logs.
4. Reconcile this host-local access seam with the library model:
   - local shared-output roots are discoverable working/output views;
   - delivery/inbox copies are staged transport only;
   - accepted plans, evidence, decisions, and supersession records must have one canonical library identity, independent of which host created a draft.
5. Provide the smallest Nix/operational implementation packet, including exact affected configuration owner/files, migration/rollback, host-by-host verification commands, and a test that Dave can read an intended non-secret artifact on both hosts while excluded paths remain inaccessible.

## Explicit non-authorizations

- Do not make any permission, group, ACL, Nix, flake, service, credential, or home-directory change from this request alone.
- Do not use one-off `chmod`, `setfacl`, symlink, or copied-secret workaround as a substitute for the durable configuration.
- Do not expose Tigwa's entire home or Hermes state.
- Do not move/delete/archive existing files until the library inventory, provenance, and restore review identify canonical records.

## Expected response

A concise design/build packet for Dave: recommended access topology; exact ownership/configuration boundary; non-secret shared-root contract; test/rollback evidence; any unresolved choice; and whether it can be batched with the next justified Nix rebuild.
