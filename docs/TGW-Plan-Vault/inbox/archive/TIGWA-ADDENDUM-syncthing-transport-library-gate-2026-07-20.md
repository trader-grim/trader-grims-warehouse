# Addendum: Syncthing transport does not bypass the research acceptance gate

**From:** Tigwa, for Dave
**To:** Claude
**Date:** 2026-07-20
**Related request:** `TIGWA-REQUEST-guided-research-acceptance-gate-review-2026-07-20.md`
**Status:** design review only; no implementation authorization

## Dave’s clarification

Syncthing may resolve file movement/reconciliation for all staged research submissions. It is transport and recovery substrate only.

The operator acceptance gate remains at the authoritative library. Arrival, conflict resolution, version history, checksum match, or successful synchronization must never promote a research artifact. The library acceptance action identifies the specific submitted artifact/version, proposed category/destination, provenance, and intended role; Dave explicitly accepts or declines it there.

Please incorporate this transport-versus-authority separation into the pending review. Do not implement Syncthing, library, schema, or workflow changes.