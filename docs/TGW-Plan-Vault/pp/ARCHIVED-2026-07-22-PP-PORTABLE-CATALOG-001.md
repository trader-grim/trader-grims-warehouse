# PP-PORTABLE-CATALOG-001 — offline/portable catalog sync (full detail)

## PP-PORTABLE-CATALOG-001 — offline/portable catalog sync (Flutter) — first real design doc 2026-07-11
**Given its own heading, pulled OUT of the "Done" rollup — it was never
actually done.** Real, substantive code exists (Dio offline data layer,
sqflite outbox, snapshot-atomic-sync) but has **never been installed on
a1131** (its target device), never live-verified, and a documented
precedent exists of this exact feature self-marking "done" while the
Flutter build was actively failing (`SUGGESTIONS.md:209-210`, todo #151).
Deep architecture review (2026-07-11, Dave: "see where it lacks or
shines") found real structural gaps, not just missing tests: connectivity
detection is 100% manual despite the packages for automating it being
installed and unused; zero conflict resolution; offline reads don't
reflect the device's own queued edits; no retry cap on failed mutations;
several sync-state UI providers are computed and never rendered; and
**the backchannel Dave flagged as still-needed is confirmed missing** — no
server-initiated communication of any kind exists. A planning doc
(`PP-EVENTD-001-design.md`) had separately and incorrectly claimed a
Flutter HTTP listener was "already implemented" — corrected same day.
The backchannel fix is PP-EVENTD-001's own already-scoped Phase 5 (Flutter
HUD WebSocket) — not new work, just now confirmed necessary rather than
assumed-someday. Full assessment + phased remediation plan (Phase A:
harden the existing manual model; Phase B: build the backchannel, depends
on PP-EVENTD-001; Phase C: conflict resolution, needs its own design pass):
`pp/PP-PORTABLE-CATALOG-001.md`.

**Correction, 2026-07-17 (Dave) — the real problem is more basic than any
of the above:** "forget all the detection and crap. These two known
devices are sitting right next to each other and I have never even seen
it fire up a single time." Two known devices, same LAN, zero network
complexity — and the app has never successfully launched/connected for
Dave even once. This is not a connectivity-detection edge case or a
conflict-resolution gap (the Phase A/B/C plan above) — it's a "does the
thing even start" problem underneath all of it, and it takes priority
over the phased remediation plan, not a peer item on the same list.
**Also revealed:** Dave already had Tigwa build a wrapper "to get to tgw
without futzing around" — meaning there's an existing, currently
undocumented workaround already in use in place of the Flutter app for
reaching `tgw`. Not yet located in the plan vault; needs finding (ask
Tigwa or Dave directly) and documenting before more Flutter work is
speced, since it may already solve part of what Flutter was meant to do.
**Next session should start here** — verify the basic launch/connect path
on a1131 (or wherever the app runs) before touching Phase A/B/C at all.

