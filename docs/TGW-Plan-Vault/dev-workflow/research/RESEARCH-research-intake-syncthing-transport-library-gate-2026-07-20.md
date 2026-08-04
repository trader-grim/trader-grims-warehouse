# Clarification: Syncthing transports research; the library accepts it

**Status:** retained design clarification — no implementation authorized
**Owner:** Dave; Tigwa maintains staging/provenance
**Date:** 2026-07-20
**Companions:**
- `RESEARCH-all-research-submissions-operator-acceptance-gate-2026-07-20.md`
- `RESEARCH-perplexity-guided-research-operator-acceptance-gate-2026-07-20.md`

## Direction

Syncthing may resolve the physical movement and reconciliation of research-submission files between devices and staging locations. That transport function is valuable, but it is not an authority function.

The operator acceptance gate remains at the authoritative library.

## Consequences

- A file arriving through Syncthing is still `capture-staged`, regardless of device, folder, or successful synchronization state.
- File movement, conflict resolution, version history, checksum match, or replication completion never equals acceptance, canonization, or implementation authorization.
- The library’s acceptance action must identify the submitted artifact/version, proposed target/category, provenance, and intended role; Dave explicitly accepts or declines it there.
- Syncthing remains recovery/transport substrate. The library remains the policy and acceptance boundary.

## Non-goals

This does not authorize Syncthing configuration changes, new folders, a library UI/schema, automated promotion, or migration of existing research.