# PP-HARDWARE-001 — IT / hardware track

**Opened 2026-07-11.** Previously only referenced by name from two other
docs (`PROPOSED-PLAN-2026-06-19.md`, `PP-AIOPS-001-cat-herding-platform.md`
— both "GPU upgrade / PP-HARDWARE-001") — never had its own heading or
design doc. Dave, triaging `#1136` (drive-space re-evaluation): "it and
#1136 and similar need an IT or hardware PP."

## Business philosophy (Dave, 2026-07-11, verbatim — governs every decision here)

> "We are doing pretty good for a PC I bought for $10 and upgraded from my
> parts box and my laptop, but I know we need better ASAP. We get it
> running, we make money, we get server. We no make money we use this
> thing."

Bootstrap hardware until revenue justifies real infrastructure — matches
[[project-ai-cost-budget]]'s standing constraint (tight until revenue-
positive). Every hardware decision here is judged against this: cheap,
incremental, works-with-what-we-have, not "buy the right thing" spending.

## Near-term concrete plan (Dave's own words, next chance to buy)

- Have: a 1TB USB-key SSD.
- Buy: an M.2-to-SATA adapter; replace an existing HDD with the 1TB SSD via
  that adapter.
- Buy: a 4-bay SSD enclosure/dock; combine with 4 spare SSDs already on
  hand for a decent-performance storage tier.
- Heat sinks all around on the SSDs.

None of this is scoped into concrete todos yet — captured as stated intent,
not yet broken into buildable steps.

## Absorbed

- **`#1136`** — drive-space re-evaluation (full physical-disk-fleet audit +
  `DRIVE-REGISTRY.md` refresh). Already partly investigated in the master
  plan's "Drive-space re-evaluation" section (2026-07-04): `/opt/TGW` was at
  83% used/48G free even before this session's new work; sdb absent,
  sdc repartitioned into backup services, no free disk currently exists to
  grow `vg_tgw` into. That section stays the detailed record; this PP is
  its new home.
- GPU upgrade (referenced from `PP-AIOPS-001`'s Phase 5 and
  `PROPOSED-PLAN-2026-06-19.md`) — inference performance, timing is
  operator call, not yet scoped.
- **Android/Tasker emergency annunciator** (Dave + Tigwa, planned together
  2026-07-13, proposal: `inbox/TIGWA-PROPOSAL-android-tasker-emergency-annunciator-20260713.md`,
  filed by Claude same session) — dedicate one of Dave's existing
  Android/Tasker devices as a local, battery-backed, LAN-only human
  alarm+ACK panel for tgw-prod/Tigwa-lite incidents. V1: a1131's
  independent watchdog (see `#1346` below) POSTs a local HTTP alert to a
  Tasker listener → siren/TTS/full-screen/vibrate until Dave taps
  acknowledge → ACK posts back to a1131. No Telegram/cloud/NATS/router/flake
  change required for v1; explicitly a human-alarm appliance, not the
  canonical event bus or business-state authority. V2/V3 (local broker
  bridge, full emergency fabric) are follow-on, not required now.
  **Coordination note (Claude, 2026-07-13):** this proposal's v1 producer
  is the same a1131 watchdog script (`tgw_prod_reachability_watch.py`)
  that `#1346`/`PP-HERMES-EA-001` just delegated to Tigwa to formalize
  (Telegram delivery fix). Both pieces of work should land as one
  coherent alerting fabric on that script, not built independently —
  flagged to Tigwa via the inbox request for #1346.
  Open decisions (Dave's call, not yet made): which device, which Tasker
  plugin path, physical placement, alarm/repeat/ack behavior, SMS/call
  fallback, VLAN placement. Acceptance: internet disconnected, LAN Wi-Fi
  up, alert still produces local audible alarm and a full ACK round-trip.

## Open — genuinely unresolved, flag for a dedicated pass, not solved here

**Immediate question (Dave): where should the knowledge-hub work (Concept
2 / `PP-KNOWLEDGE-001`) physically live so it doesn't fill `/opt/TGW`?**
`/opt/TGW`'s NVMe is already tight (83% used before any of today's new
knowledge-hub/annex/Recoll work). The existing design already points away
from the NVMe for bulk data — git-annex's tiered remotes (local + GDrive,
`PP-ANNEX-001`) and the drive-space section's power-tiered inventory (sdc/
sdi = bus-powered always-on tier; sdd/sdh = powered-dock rotating tier) are
the intended homes for archive-scale data, not `/opt/TGW` itself — but this
hasn't been explicitly confirmed as the answer to *this specific* question
and needs Dave's sign-off, not assumed.

**"A real analysis of what we need, what we want, what we will need"**
(Dave) — explicitly NOT done. This PP is a placeholder for that analysis,
not a substitute for it. Needs its own dedicated planning pass: current
capacity vs. near-term hardware plan above vs. actual growth trajectory
(the master plan's own scale-context note: "I have half a million items
here ready to process" once the pipeline is fixed — heading toward ~9x
scale).

## Cross-links
- `PP-DRIVE-INDEX-001` — drive survey/dedup tooling, the mechanism for the
  audit this PP needs.
- `PP-KNOWLEDGE-001` / `PP-ANNEX-001` — the specific "where does this data
  live" question above.
- [[project-ai-cost-budget]], [[project-scale-context]] (memory).
