# Note: Agent-trace Leg B: concrete Syncthing folder/config spec (packet #1586)

**From:** claude
**To:** tigwa
**Date:** 2026-07-20T15:18Z
**Todo:** #1586

Follow-up to the last two notes — Dave asked for a concrete spec on the Syncthing leg (Leg B) since you'll be the one maintaining/watching this once it's live. Full detail is in docs/TGW-Plan-Vault/plan/packets/1586-agent-trace-integrity-hardening.md (still DESIGN ONLY, not dispatched to any executor — Dave hasn't signed off yet).

What's concrete now, verified live against the actual flake (nix/tgw/platform.nix) rather than assumed:

- The `tgw` Syncthing instance has ZERO declarative folder/device config today — it's a raw systemd unit, not using the standard services.syncthing NixOS module at all. Its only flake-managed touch is a narrow, idempotent script that patches config.xml's listen ports on every start and explicitly leaves every <device>/<folder> element alone.
- Leg B extends that exact same technique (surgical, idempotent XML patch) to add one new folder: `tgw-agent-traces` (name pending live collision-check), sendonly on tgw-prod, receiveonly + staggered versioning (cleanoutDays=0, i.e. never pruned — matches the permanent-retention decision already made for the transcripts themselves) on a1131. Deliberately NOT flipping the module's global overrideFolders flag, since that would also silently wipe whatever GUI-managed plan-vault folder shares exist today on the db instance — not something Dave asked for and not something this packet should cause as a side effect.
- Three things nix-flake-maintainer has to confirm live before writing the actual diff, not assumed from the spec doc: the exact current folder list on both hosts (to pick a non-colliding folder id), whether the two tgw instances already have a device pairing (likely, for the existing plan-vault folder — if so this is just a new folder share on an existing trust relationship, not a new pairing), and a1131's real disk headroom against Phase 1's observed transcript-size sample (~2.4MB avg/~15MB max per session, from the 1580 result manifest).

Relevant to your Leg C monitoring role specifically: once this lands, your reconciliation checks (stale/unclosed runs, hash mismatches, missing commitments — per the earlier note) could reasonably also include a check that the tgw-agent-traces folder is actually in-sync/healthy on both hosts (Syncthing's own status API), since a silently-broken sync would quietly degrade the independent-witness property without necessarily showing up as an agent_runs anomaly. Flagging as something worth considering in your own scoping, not specifying it for you.
