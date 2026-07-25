# Clarification — Portable Fleet primary outcome: “morning coffee” operations

**Dave’s success criterion:** With his laptop over morning coffee, Dave can operate TGW as though he were seated at the primary system.

## Functional meaning

This is not merely VPN reachability or a remote shell. The portable operator surface must let Dave, through approved Tailscale-connected interfaces:

1. see current truthful system health, queue/work state, blocked work, and next eligible work;
2. search and inspect inventory, photos, listing/pipeline evidence, and relevant history;
3. create, resume, reorder, pause, and work a human or AI-assisted work queue continuously, including the Next Item handoff;
4. inspect and correct authorized operational data with field-level error/recovery guidance;
5. take an explicitly authorized action (including an external action only after its named confirmation/gate) and see the durable resulting state and next work;
6. reach the same authoritative evidence and decision paths without scavenger-hunting among hosts, terminals, or stale replicas.

## V1 acceptance: Laptop Coffee Console

The first laptop cohort is complete only when a named laptop can, over Tailscale:

- launch the Nix-managed `tgw` client and approved browser/app surface;
- query the canonical tgw-prod state-machine read model and current health/queue summary;
- open a selected item and its history/evidence;
- complete one bounded, non-external workflow item and receive the next eligible item with context;
- show an intentionally held/blocked item and its prerequisite rather than hiding it;
- preserve all actions/outcomes in the canonical ledger;
- lose/recover connectivity with clear degraded state and no silent local authority.

## Boundary

Functional equivalence does not create a second production database, worker fleet, secret store, or broad remote-desktop bypass. The portable device remains a named, revocable, least-privilege client of tgw-prod. Camera capture and tablet workflows extend the same contract after the laptop path proves it.
