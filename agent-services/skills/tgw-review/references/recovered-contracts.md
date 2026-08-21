# Recovered TGW review contracts

The current skill reconciles two Claude-era skills recovered from Tigwadev
archives and application Git history.

## `tgw-pr-review`

- Introduced/restored at application commit
  `234ff848dc6fee1301623be39c688eb7cc6ab119`.
- Recovered blob:
  `9a267214fd4efec051af38173ac9b3cfc50a4fd8`.
- Retained: inspect commits and diff, run relevant tests/lint, check invariants,
  report precise bugs and deployment implications.
- Retired: assuming `main...HEAD`, treating an unbound full suite as admission
  evidence, referring to a fixed count of legacy invariants, and returning an
  unbound `LGTM` verdict.

## `tgw-runner-review`

- Added at application commit
  `16e4d850921168051ef99b5d0ab2208d35f64795`; last historical update at
  `0925a65725cbe1aa16338e425037220113e07c31`.
- Recovered final blob:
  `f59f653b2922b66ea0523c3abc28a68e0bf0156d`.
- Retained: one exact candidate per review, manifest sanity, spec and
  out-of-scope fidelity, real execution evidence, explicit findings,
  independence, and no self-merge.
- Retired: the legacy `docs/TGW-Plan-Vault` authority path, mandatory Todo
  packet/branch naming, the proposed two-fix cap, writes into the retired
  embedded Plan checkout, silent pass-through, and a Claude-specific stitch
  handoff.

## Current authority resolution

The surviving contract is governed by the exact approved Plan/solution and
execution card returned for the requested review by the catalog-bound Context
MCP. The canonical Plan lives in `/opt/TGW/library/plans`; its current evidence
HEAD must be a clean descendant of the immutable approved commit. The
application repository's historical skill blobs and hard-coded historical
Plan commits are provenance, not current Plan authority.
