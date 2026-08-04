# Note for next startup — listing revision/draft-update process still wrong

Dave, 2026-07-16, end of the kdeconnect-clipboard-triage-incident session — flagged
this to be picked up at next session start, not addressed now.

**Evidence item:** `tgw202605040949058` — live eBay data still differs from what the
web UI (LISTEDITOR / revision-apply flow, PP-LISTEDITOR-001) shows/claims after an
update. The update reports "succeeds" but the actual live listing state doesn't match.

**Dave's framing:** this is evidence the draft/update/revise process for listings is
still not correct — not a one-off glitch. Relevant prior work: C4 (offer PUT must carry
full-replace body), C12/C13 (field-set envelopes, gated Set A writes), LISTEDITOR Phase 2
revision-apply (`project-listeditor-phase2` memory — "code complete, drift-gated live PUT,
live-fire test pending"). This item may be exactly the live-fire evidence that phase was
waiting on, or a new failure mode on top of it.

**Ask:** investigate `tgw202605040949058` specifically — compare the live eBay listing
state against what the update call claimed to write and what the web UI shows. Then:
Dave asked whether this warrants its own specialist (agent profile) or additional skills,
similar to today's `nix-flake-maintainer` build — worth considering once the actual
failure mode is understood, not before.

**Do not address at the moment this note is filed** — this is a startup-pickup item,
per Dave's explicit instruction.
