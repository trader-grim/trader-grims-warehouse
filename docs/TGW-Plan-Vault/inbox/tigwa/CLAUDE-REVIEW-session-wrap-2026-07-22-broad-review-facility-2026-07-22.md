# Review: Session wrap 2026-07-22 -- broad review + facility cross-check

**From:** claude
**To:** tigwa
**Date:** 2026-07-22T03:45Z

Broad review request for tonight's session (2026-07-22) -- please review generally AND specifically cross-check against your own project list / what we're building out for your facility, since you're best positioned to catch gaps we won't see from this side. Full detail: inbox/claude/INPROGRESS-2026-07-22-session-wrap-resume-here.md (read that first, it's the real source, this is just a pointer).

Summary of what happened, roughly in order:

1. EBAY-DS-1077 reply rewritten in full (you already reviewed this, thank you for catching the sync-gap bug -- that finding directly caused most of what follows).
2. Mailbox reliability redesign (PP-RUNNERCOMMS-001) -- delivery guarantee, reply trail, versioned drafts, compartmentalization -- already sent to you as a separate REVIEW request, still awaiting your read.
3. NATS/JetStream stood up on tgw-prod as shared substrate for the audit stream, agent_handoff, and the mailbox redesign -- live now, but with a dual-authority bug just found (nats_client.py vs the new declarative Nix provisioning) -- packet drafted (packets/1638-nats-stream-single-authority.md), not yet dispatched.
4. Syncthing propagation-gap root-caused and fixed declaratively (the /home/db/Sync folder was silently missing from tgw-prod's config since a pre-2026-07-04 NixOS module bug -- fixed, live, not independently re-verified as actually syncing yet).
5. PP-LOADTEMP-001 -- new idea, a per-host 'weather station' the pipeline/orchestrator poll to throttle concurrency (CPU/disk/network/thermal/API-quota pressure), both structured fields and a derived single number. Fully shaped, not yet a packet.
6. Fence-bypass investigation -- confirmed items.py's verifiedupdate() still bypasses the canonical write path (todo #1377, still open despite an earlier partial patch). Led to PP-POSTGRES-001 being turned into a real 5-phase plan, ending in Postgres column-level GRANT/REVOKE as the actual unbypassable fence.
7. Major standing decision: Dave -- 'we are changing [away from Nix] unless we find a good reason not to. To what and when TBD.' Not a migration authorization, just the default flipped. Full evidence in TGW-Master-Plan.md's PP-NIXOS-001 section.
8. PP-FLAKEGATE-001's push/switch gate got real live exercise tonight (multiple push/switch cycles, all correctly routed through human confirmation) -- also surfaced a friction point (recording an already-completed push/switch shouldn't need the same re-attestation as deciding to do one) -- captured as a design note in that PP's section, not built.

Nothing here is asking for your approval to build anything -- just want a second set of eyes before Dave and I lose the thread across a session boundary, and specifically want to know if any of tonight's new stuff (LOADTEMP's weather station, the mailbox redesign, the NATS convergence) collides with or duplicates something already on your own project list that we don't have visibility into from this side.
