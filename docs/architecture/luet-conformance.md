# Luet conformance boundary

TGW pins Luet `0.9.26` at upstream commit
`48f17dbc7a9edb94b1415a2eeeac4e5c2d45f5d3`. The source-only Nix package is
`nix/luet.nix`; it verifies the upstream archive and uses the vendored Go graph.

Luet is an independent SAT check for the subset it represents exactly:
conjunctive dependency closure, unique capability providers, and package
conflicts. TGW translates these to Luet packages, asks Luet for the target
dependency closure, maps the packages back to provider IDs, and only then
normalizes an equal provider closure to the canonical TGW closure hash.

Luet `0.9.26` does not directly preserve all `tgw-plan/v2` semantics:

- nested `any` requirement expressions;
- ranking complete solutions by TGW provider preference;
- typed `UNKNOWN_CAPABILITY`, `UNSATISFIED`, and `BLOCKED` outcomes;
- observed-state/evidence reuse, supersession, invalidation, and work-unit
  projection.

The adapter therefore returns `UNREPRESENTABLE` and `available: false` when a
capability has multiple available providers, any nested `any` is present, or a
selected provider carries a nonzero preference. It never copies the native
hash into a purported Luet result for those graphs. Dispatch remains held until
another pinned provider represents the full semantics or the canonical graph
falls within the proven subset.
