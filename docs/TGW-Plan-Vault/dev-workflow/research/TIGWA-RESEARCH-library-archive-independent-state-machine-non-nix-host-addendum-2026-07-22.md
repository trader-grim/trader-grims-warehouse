# Addendum — library host may deliberately be non-Nix

**Status:** Staged research clarification, not an implementation authorization.
**Parent research:** `TIGWA-RESEARCH-library-archive-independent-state-machine-2026-07-22.md`
**Operator direction:** Dave noted that an independent library/archive authority can have its own non-Nix OS; shared Nix ownership is not a prerequisite or desired coupling for the library perimeter.

## Implication

The library host should be specified by its independent authority and recovery contract, not by matching tgw-prod's OS or deployment stack. A non-Nix host can reduce common-mode configuration and toolchain failure, while keeping the library implementation portable and operated through explicit interfaces.

This does not imply an OS choice, host procurement, service installation, credentials, network exposure, or deployment. A future bounded design packet must compare the operational/recovery implications of candidate host platforms and retain a documented, independently testable rebuild procedure.
