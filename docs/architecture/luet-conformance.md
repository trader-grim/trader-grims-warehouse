# Luet conformance boundary

TGW pins Luet `0.9.26` at upstream commit
`48f17dbc7a9edb94b1415a2eeeac4e5c2d45f5d3`. The source-only Nix package is
`nix/luet.nix`; it verifies the upstream archive and uses the vendored Go graph.

Luet is an independent SAT check for the provider closure. TGW translates each
capability to a Luet virtual package, each TGW provider to a package that
provides those virtual capabilities, and nested `all`/`any` expressions to
synthetic selector packages. Luet therefore receives every available provider
and chooses a satisfiable dependency/conflict closure; the adapter maps that
closure back to TGW provider IDs and only then normalizes an equal closure to
the canonical TGW closure hash.

Luet `0.9.26` does not directly preserve all `tgw-plan/v2` semantics:

- typed `UNKNOWN_CAPABILITY`, `UNSATISFIED`, and `BLOCKED` outcomes;
- observed-state/evidence reuse, supersession, invalidation, and work-unit
  projection.

TGW computes its canonical ranking of complete closures after the Luet SAT
result. Provider preference is retained as TGW metadata rather than silently
reinterpreted as a Luet package version or solver preference. If Luet selects a
different satisfiable closure, the adapter returns `DISAGREEMENT` with the
observed provider IDs and never copies the native hash. That disagreement holds
dispatch. Typed diagnostics, observations, invalidation, and work-unit
projection remain TGW-native data around the shared provider closure.
